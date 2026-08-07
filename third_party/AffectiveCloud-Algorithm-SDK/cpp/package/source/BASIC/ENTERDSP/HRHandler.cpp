#include "HRHandler.h"
#include "DSPHR.h"
#include "Common.h"
#include "Basic.hpp"
namespace basic
{
    namespace dsp
    {
        namespace hrhandler
        {
            HRHandlerResult handler(const vectori &hrRawData, double splitLen, DeviceInfo *deviceInfo,
                                    HRHandlerTemp &tmp, double scopeLim, double validCheckLen)
            {
                //初始

                auto dataLen = static_cast<int>(std::round(splitLen * deviceInfo->hrFs()));
                auto stepLen = hrRawData.size();
                auto intervalStoreLen = std::max(stepLen, static_cast<size_t>(scopeLim * deviceInfo->hrFs())) + 25;
                HRHandlerResult res;
                res.hrv = 0;
                res.hr = 0;
                res.interval = 0;
                res.quality = INVALID;
                
                HRPower powerTmp;
                res.power = powerTmp;
                res.syncCor = 0;

                vectord validData;
                //数据预处理
                for (auto &e : hrRawData)
                {
                    if (e >= 10)
                        validData.push_back(static_cast<double>(e));
                }
                if (validData.size() < 1)
                {
                    tmp.hrData = nc::NdArray<double>();
                    tmp.hrStore = nc::NdArray<double>();
                    tmp.hrWaveStore = nc::NdArray<double>();
                    tmp.validCount = 0;
                    tmp.intervalStore = nc::NdArray<double>();
                    return res;
                }
                                     
                auto ncValidData = nc::NdArray(validData);
                // ---首段有效性检验（只在输入有效数据的初始阶段检验）
                
                if (tmp.validCount < static_cast<int>(std::round(validCheckLen / (stepLen / deviceInfo->hrFs()))))
                {
                    tmp.validCount += 1;
                    //首段去除（在初始阶段的该范围内无效数据较多，直接去除）
                    if (tmp.validCount * stepLen < 24)
                        return res;
                    // 异常检测（连续监测一段心率数据，观察变化程度是否超出阈值）
                    tmp.hrStore = nc::hstack({tmp.hrStore, ncValidData});
                    if (tmp.hrStore.size() > 30)
                        tmp.hrStore = tmp.hrStore[nc::Slice(tmp.hrStore.size() - 30, tmp.hrStore.size())];
                    if (tmp.hrStore.size() < 30 || nc::stdev(tmp.hrStore).item() > 3)
                        return res;
                }
           
                //数据拼接
                tmp.hrData = nc::hstack({tmp.hrData, ncValidData});
                
                if (tmp.hrData.size() < static_cast<size_t>(dataLen))
                    return res;
                tmp.hrData = tmp.hrData[nc::Slice(tmp.hrData.size() - std::max(stepLen, static_cast<size_t>(dataLen)), tmp.hrData.size())];

                //心率值计算
                res.hr = nc::mean(tmp.hrData[nc::Slice(tmp.hrData.size() - stepLen, tmp.hrData.size())]).item();

                //心跳间期计算
                
                auto stepData = tmp.hrData[nc::Slice(tmp.hrData.size() - stepLen, tmp.hrData.size())];
                auto hrIntervalSeqTmp = hrIntervalCal(stepData.toStlVector());
  
                auto hrIntervalSeq = nc::NdArray(hrIntervalSeqTmp);
                tmp.intervalStore = nc::hstack({tmp.intervalStore, hrIntervalSeq});
                res.interval = nc::mean(hrIntervalSeq).item();
                
                if (tmp.intervalStore.size() >= intervalStoreLen)
                    tmp.intervalStore = tmp.intervalStore[nc::Slice(tmp.intervalStore.size() - intervalStoreLen, tmp.intervalStore.size())];
                nc::NdArray<double> curIntervalStore;
                if (tmp.intervalStore.size() > static_cast<size_t>(scopeLim * deviceInfo->hrFs()))
                {
                    curIntervalStore = tmp.intervalStore[nc::Slice(tmp.intervalStore.size() - static_cast<size_t>(scopeLim * deviceInfo->hrFs()), tmp.intervalStore.size())];
                }
                else
                {
                    curIntervalStore = tmp.intervalStore;
                }
                    
                auto vectorIntervalStore = curIntervalStore.toStlVector();
              
                //心率变异性计算
                res.hrv = hrvValCal(vectorIntervalStore, scopeLim, deviceInfo);

                // 频段能量计算
                if (curIntervalStore.size() >= static_cast<size_t>(scopeLim * deviceInfo->hrFs()))
                {
                    res.power.power = hrvPowerCal(vectorIntervalStore, 0, 0.4, deviceInfo->hrFs());
                    res.power.hf = hrvPowerCal(vectorIntervalStore, 0.15, 0.4, deviceInfo->hrFs());
                    res.power.lf = hrvPowerCal(vectorIntervalStore, 0.04, 0.15, deviceInfo->hrFs());
                    res.power.vlf = hrvPowerCal(vectorIntervalStore, 0.003, 0.04, deviceInfo->hrFs());
                }
                else
                {
                    res.power.power = 0;
                    res.power.hf = 0;
                    res.power.lf = 0;
                    res.power.vlf = 0;
                }
                //神经系统同步频率能量计算
                
                tmp.hrWaveStore = nc::hstack({tmp.hrWaveStore, ncValidData});
                     
                auto hrWaveStoreSize = tmp.hrWaveStore.size();
                if (hrWaveStoreSize > 130)
                    tmp.hrWaveStore = tmp.hrWaveStore[nc::Slice(hrWaveStoreSize - 130, hrWaveStoreSize)];
                
                hrWaveStoreSize = tmp.hrWaveStore.size();
                
                if (hrWaveStoreSize >= 130) //心率波形计算（将心率值降采样至1Hz，此处采用均值降采样）
                {
                    auto hrWaveStoreCopy = tmp.hrWaveStore;
                    auto hrWaveReshape = hrWaveStoreCopy.reshape(-1, deviceInfo->hrFs()); //按采样率进行重排，每行为1秒钟的数据
                 
                    auto hrWave = nc::mean(hrWaveReshape, nc::Axis::COL); 
                    vectord hrWaveVector = hrWave.toStlVector();
                    
                    if (mathtool::stdv(hrWaveVector, 1) > 0) {
                        vectord corList;
                        vectord fpList = {0.07, 0.08, 0.09, 0.1, 0.11, 0.12};
                    
                        for (auto &e : fpList)
                        {
                            auto cor = waveFreqCor(hrWave, e, 1);
                            corList.push_back(cor);
                        }
                        auto hrRange = tmp.hrWaveStore.max().item() - tmp.hrWaveStore.min().item();
                        auto syncCorFactor = hrRange / 5.0 > 1.0 ? 1.0 : hrRange / 5.0;
                        res.syncCor = *std::max_element(corList.begin(), corList.end()) * syncCorFactor;
                    }
                    else
                    {
                        res.syncCor = 0;
                    }
                }
                else
                    res.syncCor = 0;
                res.quality = VALID;

                return res;
            }
        }

    } // namespace dsp

} // namespace basic
