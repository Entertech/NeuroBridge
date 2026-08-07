#include "Device.h"
#include "Data.h"
#include "Sleep.h"
#include "Pretreat.h"
#include "Feature.h"
#include "SleepHandler.h"
#include "MathTool.h"
#include "Basic.hpp"

using namespace basic;
using namespace affection;
using namespace dsp;
namespace ac {
    SleepComputing::SleepComputing() {
        temp.index = 0;
        temp.time_cum = 0;
        temp.sleepDegreeTmp = 0.;
        temp.sleepStateTmp = 0;
        temp.sleepStageTmp = 0;
        temp.movementAmpTmp = 0;
        temp.arousalPowerTmp = 0;
        temp.sleepTmp.adjFinishTime = 0;
        temp.sleepTmp.sleepState = define::SleepStateEnum::AWAKES;
        temp.sleepTmp.sleepStage = define::SleepStage::WAKE;
        temp.sleepTmp.latencyTime = INT_MAX;
        temp.sleepTmp.sleepFlag = false;
        temp.sleepTmp.sleepPhaseStore = nc::zeros<int>(1, 20);
        temp.sleepTmp.sleepProbStore = nc::zeros<double>(1, 7);
        temp.sleepTmp.eegQualityStore = nc::zeros<int>(1, 7);
        temp.sleepTmp.sleepStageStore = nc::zeros<int>(1, 5);
        temp.sleepTmp.sleepStateLen = 0;
        temp.sleepTmp.model = nullptr;
    }

    SleepComputing::~SleepComputing() {
        if (temp.sleepTmp.model != nullptr)
            svm_free_and_destroy_model(&temp.sleepTmp.model);
    }

    /// 实时触发
    /// \param cache 会话内部缓存
    /// \param eegData 解析后的左通道脑电数据
    /// \return 睡眠程度，睡眠状态
    SleepTriggerRes SleepComputing::trigger(SessionCache &cache, std::vector<double> &eegData) {
        dsp::DeviceInfoEyeShade *deviceInfo = new dsp::DeviceInfoEyeShade();
        auto stepSec = eegData.size() / deviceInfo->eegFs(); //步长

        // 读取外部缓存
        auto eegQuality = cache.eegQuality;
        auto sleepDegree = 0.;
        auto sleepState = 0;
        auto sleepStage = 0;
        vectord eegTypeFeature;

        //有效性判断
        auto eegValid = dsp::eegLoadCheck(eegData, deviceInfo);
        if (eegQuality == EEGQuality::NONE)
            temp.wearRec.push_back(0);
        else
            temp.wearRec.push_back(1);

        //执行算法
        if (eegValid) {

            //数据拼接
            temp.eegSplitData.insert(temp.eegSplitData.end(), eegData.begin(), eegData.end());
            auto eegLen = temp.eegSplitData.size();
            //睡眠计算
            if (temp.eegSplitData.size() < 6 * deviceInfo->eegFs()) {

                eegTypeFeature = {0, 0, 0};
            } else {
                //数据裁剪
                size_t splitSize = static_cast<size_t>(round(30 * deviceInfo->eegFs()));
                if (splitSize < temp.eegSplitData.size())
                {
                    for (size_t i = 0; i < splitSize; ++i) {
                        temp.eegSplitData[i] = temp.eegSplitData[i + eegLen - splitSize];
                    }
                    temp.eegSplitData.resize(splitSize);
                }

                //特征提取
                auto eegFeatures = sleepEEGFeatures(temp.eegSplitData, 1000, deviceInfo);
                eegTypeFeature = eegFeatures.eegTypeFeature;
                eegQuality = eegFeatures.quality;
                if (temp.eegPower.power > 0)
                    temp.eegPower = mathtool::eegPowerSmoothAvg(eegFeatures.eegPower, temp.eegPower, 0.7);
                else
                    temp.eegPower = eegFeatures.eegPower;
                //计算睡眠状态
                nc::NdArray eegFeatureNc(eegFeatures.eegFeature, false);
                auto sleepValue = handler::sleepHandler(
                        eegFeatureNc,
                        temp.eegPower,
                        static_cast<int>(eegQuality),
                        temp.time_cum,
                        temp.sleepTmp,
                        2, 2);
                sleepDegree = sleepValue.sleepDegree;
                sleepState = sleepValue.sleepState;
                sleepStage = sleepValue.sleepStage;
                temp.time_cum += stepSec;
            }
        } else {
            sleepDegree = temp.sleepDegreeTmp;
            sleepState = temp.sleepStateTmp;
            sleepStage = temp.sleepStageTmp;
            eegTypeFeature = {0, 0, 0};
            eegQuality = EEGQuality::NONE;
        }

        temp.sleepDegreeTmp = sleepDegree;
        temp.sleepStateTmp = sleepState;
        temp.sleepStageTmp = sleepStage;

        // 进阶分析
        auto eegSpindleRate = eegTypeFeature[0];
        auto waveAmp = eegTypeFeature[1];
        auto arousalPower = eegTypeFeature[2];
        auto movement = 0;
        auto arousal = 0;
        if ((waveAmp - temp.movementAmpTmp > 3000) && waveAmp > 5000)
            movement = 1;
        if (temp.arousalPowerTmp > 0)
            if ((arousalPower - temp.arousalPowerTmp > 15) && 300 < waveAmp && waveAmp < 3000)
                arousal = 1;
        temp.movementAmpTmp = waveAmp;
        temp.arousalPowerTmp = arousalPower;

        // 更新内部缓存
        temp.index += 1;
//        temp.time_cum += stepSec;
        temp.sleepDegreeRec.push_back(sleepDegree);
        temp.sleepStateRec.push_back(sleepState);
        temp.sleepStageRec.push_back(sleepStage);
        temp.sleepEEGAlphaRec.push_back(temp.eegPower.alphaNorm());
        temp.sleepEEGBetaRec.push_back(temp.eegPower.betaNorm());
        temp.sleepEEGThetaRec.push_back(temp.eegPower.thetaNorm());
        temp.sleepEEGDeltaRec.push_back(temp.eegPower.deltaNorm());
        temp.sleepEEGGammaRec.push_back(temp.eegPower.gammaNorm());
        temp.sleepEEGHighBetaRec.push_back(temp.eegPower.highBetaDB());
        temp.sleepEEGLowBetaRec.push_back(temp.eegPower.lowBetaDB());
        temp.sleepEEGQualityRec.push_back(eegQuality);
        temp.sleepEEGSpindleRec.push_back(eegSpindleRate);
        temp.sleepEEGMovementRec.push_back(movement);
        temp.sleepEEGArousalRec.push_back(arousal);

        delete deviceInfo;
        //输出结果
        SleepTriggerRes res;
        res.sleepDegree = nc::round(sleepDegree, 2);
        res.sleepState = static_cast<int>(sleepState);
        res.sleepStage = sleepStage;
        res.sleepSpindle = eegSpindleRate;
        return res;
    }

    SleepReportRes SleepComputing::report() {

        SleepReportRes res;
        res.awakeDuration = 0;
        res.deepDuration = 0;
        res.lightDuration = 0;
        res.sleepLatency = 0;
        res.sleepPoint = 0;
        res.remDuration = 0;
        res.movementCount = 0;
        res.arousalCount = 0;
        if (temp.sleepDegreeRec.empty())
            return res;
        //读取内部缓存
        auto sleepDegreeRec = nc::NdArray<double>(temp.sleepDegreeRec);
        auto sleepStateRec = nc::NdArray<int>(temp.sleepStateRec);
        auto sleepStageRec = nc::NdArray<int>(temp.sleepStageRec);
        auto wearRect = nc::NdArray<int>(temp.wearRec);
        //执行算法
        //---初始化
        size_t dataLen = sleepStateRec.size();
        size_t sleepPoint = 0;
        auto sleepDegreeAdj = sleepDegreeRec.copy();

        //---前端点修正
        size_t iniPoint = dataLen - 1;
        size_t validPoint = dataLen - 1;
        for (size_t i = 0; i < dataLen; i++) {
            if (sleepDegreeAdj[i] > 0) {
                iniPoint = i; //前端点
                break;
            }
        }
        for (size_t i = 0; i < dataLen; i++) {
            if (sleepDegreeRec[i] > 0) {
                validPoint = i; //睡眠程度起始有效点
                break;
            }
        }

        iniPoint = std::min(std::max(iniPoint + 10, validPoint), dataLen - 1);
        // sleepDegreeAdj[nc::Slice(0, iniPoint)] = sleepDegreeAdj[iniPoint];
        for (size_t i = 0; i < iniPoint; i++) {
            sleepDegreeAdj.at(i) = sleepDegreeAdj.at(iniPoint);
        }


        // 后端修正
        auto endPoint = 0;
        for (size_t i = 1; i < dataLen; i++) {
            if (sleepDegreeAdj[dataLen - i - 1] != sleepDegreeAdj[dataLen - i]) {
                endPoint = dataLen - i - 1; //后端点
                break;
            }
        }
        // sleepDegreeAdj[nc::Slice(endPoint, sleepDegreeAdj.size())] = sleepDegreeAdj[endPoint];
        for (size_t i = endPoint; i < sleepDegreeAdj.size(); i++) {
            sleepDegreeAdj.at(i) = sleepDegreeAdj.at(endPoint);
        }


        //---无效点处理
        if (sleepDegreeAdj[0] <= 0) {
            sleepDegreeAdj[0] = 100;
        }
        for (size_t i = 1; i < dataLen; i++) {
            if (sleepDegreeAdj[i] <= 0) {
                sleepDegreeAdj[i] = sleepDegreeAdj[i - 1];
            }
        }

        //---基线修正
        if (nc::max(sleepDegreeAdj).item() < 90) {
            auto adjRange = std::max((90 - nc::max(sleepDegreeAdj).item()) * 0.5, 80 - nc::max(sleepDegreeAdj).item());
            auto adjRate = adjRange / (nc::max(sleepDegreeAdj).item() - nc::min(sleepDegreeAdj).item() + 0.01) + 1;
            sleepDegreeAdj =
                    (sleepDegreeAdj - nc::min(sleepDegreeAdj).item()) * adjRate + nc::min(sleepDegreeAdj).item();
        }

        //---状态修正
        for (size_t i = 0; i < dataLen; i++) {
            if (sleepStateRec[i] <= 0) {
                if (sleepDegreeAdj[i] < 75)
                    sleepDegreeAdj[i] = 75 - (75 - sleepDegreeAdj[i]) * 0.9;
            } else {
                if (sleepDegreeAdj[i] > 75)
                    sleepDegreeAdj[i] = 75 + (sleepDegreeAdj[i] - 75) * 0.9;
            }
            if (i < 20 || i > dataLen - 10 || i > size_t(endPoint)) {
                if (sleepDegreeAdj[i] < 80)
                    sleepDegreeAdj[i] = 80;
            }
        }
        //---睡眠曲线计算
        auto halfSmoothLen = std::min(std::max(int(dataLen * 0.003), 5), int(dataLen));

        auto vecAdj = sleepDegreeAdj.toStlVector();
        auto sleepCurve = mathtool::smoothCurveCal(vecAdj, halfSmoothLen);
        auto sleepCurveNc = nc::NdArray(sleepCurve, false);
        // ---入睡点计算
        double decreaseCum = 0.; //睡眠程度下降累积值
        bool sleepPointCheck = false; //入睡点校验标志
        int checkCount = 0; //入睡点校验计数

        for (size_t i = 0; i < dataLen; i++) {
            //睡眠状态变化判断入睡点
            if (sleepStateRec[i] > 0) {
                sleepPoint = i;
                break; //睡眠状态直接判断入睡点，无需校验
            }
            // 睡眠程度变化判断入睡点
            if (!sleepPointCheck) {
                if (0 < sleepDegreeRec[i] && sleepDegreeRec[i] < 42) {
                    sleepPoint = i;
                    sleepPointCheck = true; //进行校验
                }
            }
            // 睡眠曲线变化判断入睡点
            if (!sleepPointCheck) {
                //睡眠曲线阈值判断
                if (sleepCurve[i] < 60) {
                    sleepPoint = i;
                    sleepPointCheck = true; //进行校验
                }
                if (sleepCurve[i] < 45) {
                    sleepPoint = i;
                    break; //睡眠曲线很低时直接判断入睡点，无需校验
                }
                //睡眠曲线下降累积判断
                if (i > 0) {
                    if (sleepCurve[i] < sleepCurve[i - 1] && sleepCurve[i] < 80)
                        decreaseCum += (sleepCurve[i - 1] - sleepCurve[i]);
                    else
                        decreaseCum = 0.;
                }
                int sleepCurvethr = 0;
                if (i < 40)
                    sleepCurvethr = 60;
                else
                    sleepCurvethr = 70;
                if (decreaseCum > 10 && sleepCurve[i] < sleepCurvethr) {
                    sleepPoint = i;
                    sleepPointCheck = true;
                }

            }
            // 入睡点校验
            if (sleepPointCheck) {
                if (sleepCurve[i] > sleepCurve[sleepPoint]) {
                    sleepPointCheck = false;
                    sleepPoint = 0;
                    checkCount = 0;
                } else
                    checkCount += 1;
                if (checkCount >= std::min(std::max(int(dataLen * 0.0015), 12), 130))
                    break;
            }
        }

        size_t stageSleepPoint = 0;
        if (sleepPoint > 0) {
            for (int i = 0; i < dataLen; ++i) {
                if (sleepStageRec[i] > 0)
                {
                    stageSleepPoint = i; // 睡眠分期入睡点
                    break;
                }
            }
            nc::NdArray<size_t> sleepPointVec = {sleepPoint, validPoint, stageSleepPoint};
            sleepPoint = nc::max(sleepPointVec).item(); // 入睡点不能早于信号有效起始点和睡眠分期入睡点

        }
        // ---睡眠分期修正
        // auto sleepStageAdj = nc::hstack({sleepStageRec[nc::Slice(3, sleepStageRec.size())], {0,0,0}}).toStlVector();
        auto sleepStageRecTmp = sleepStageRec.toStlVector();
        if (sleepStageRecTmp.size() < 3)
            return res;
        vectord sleepStageAdj(sleepStageRecTmp.begin()+3, sleepStageRecTmp.end());
        sleepStageAdj.insert(sleepStageAdj.end(), 3, 0);
        // ------入睡段修正
        if (sleepPoint > 0)
        {
            bool sleepStageIniAdjFlag = false;
            for (int i = 0; i < dataLen; ++i) {
                if (i >= sleepPoint)
                    sleepStageIniAdjFlag = true;
                if (sleepStageIniAdjFlag) // 将入睡点后的清醒期置为N1期，直至睡眠状态切换为入睡
                    if (sleepStageAdj[i] == 0)
                        sleepStageAdj[i] = 1;
                if (sleepStateRec[i] > 0)
                    break;
            }
        }

        // ------平滑修正
        vectori sleepStageCode(sleepStageAdj.size(), 0);
        vectori sleepStageSmooth(sleepStageAdj.size(), 0);
        for (int i = 0; i < sleepStageAdj.size(); i++) {
            if (sleepStageAdj[i] == 1) {
                sleepStageCode[i] = 2;
            } else if (sleepStageAdj[i] == 2) {
                sleepStageCode[i] = 3;
            } else if (sleepStageAdj[i] == 3) {
                sleepStageCode[i] = 4;
            } else if (sleepStageAdj[i] == 4) {
                sleepStageCode[i] = 1;
            }
        }
        halfSmoothLen = std::min(std::max(int(sleepStageCode.size() * 0.003), 8), int(sleepStageCode.size()));
        vectori sleepStageCodeExpand(halfSmoothLen * 2 + sleepStageCode.size(), 0);
        fill(sleepStageCodeExpand.begin(), sleepStageCodeExpand.begin() + halfSmoothLen, sleepStageCode[0]);
        fill(sleepStageCodeExpand.end() - halfSmoothLen, sleepStageCodeExpand.end(), sleepStageCode[sleepStageCode.size() - 1]);
        copy(sleepStageCode.begin(), sleepStageCode.end(), sleepStageCodeExpand.begin() + halfSmoothLen);

        auto windowLen = halfSmoothLen * 2 + 1;
        for (int i = 0; i < dataLen; i++) {
            vectord sleepStageCodeWindow(sleepStageCodeExpand.begin() + i,
                                             sleepStageCodeExpand.begin() + i + windowLen);
            int wakeCount = count(sleepStageCodeWindow.begin(), sleepStageCodeWindow.end(), 0);
            int n3Count = count(sleepStageCodeWindow.begin(), sleepStageCodeWindow.end(), 4);
            int remCount = count(sleepStageCodeWindow.begin(), sleepStageCodeWindow.end(), 1);

            if (wakeCount > windowLen * 0.5) {
                sleepStageCode[i] = 0;
            } else {
                if (n3Count > windowLen * 0.6) {
                    sleepStageCode[i] = 4;
                } else {
                    if (remCount > windowLen * 0.2) {
                        sleepStageCode[i] = 1;
                    } else {
                        auto meanSleepStages = mathtool::mean(sleepStageCodeWindow);
                        int roundedSleepStages = (int)round(meanSleepStages);
                        sleepStageCode[i] = roundedSleepStages;
                    }
                }
            }
        }

        for (int i = 0; i < sleepStageCode.size(); i++) {
            if (sleepStageCode[i] == 1) {
                sleepStageSmooth[i] = 4;
            } else if (sleepStageCode[i] == 2) {
                sleepStageSmooth[i] = 1;
            } else if (sleepStageCode[i] == 3) {
                sleepStageSmooth[i] = 2;
            } else if (sleepStageCode[i] == 4) {
                sleepStageSmooth[i] = 3;
            }
        }

        // ------融合（对于长数据，深睡点或特定时长之后到最后的清醒段采用平滑值）
        if (sleepStageAdj.size() * 6 > 3600) {
            int sleepStageComStart = 600;
            int sleepStageComFinish = -1;
            for (int i = 0; i < sleepStageAdj.size(); i++) {
                if (sleepStageAdj[i] == 3) {
                    sleepStageComStart = i; // deep point
                    break;
                }
            }
            for (int i = 0; i < sleepStageAdj.size(); i++) {
                if (sleepStageAdj[sleepStageAdj.size()-(i + 1)] > 0) {
                    sleepStageComFinish = sleepStageAdj.size()-(i + 1); // wake point
                    break;
                }
            }
            for (int i = sleepStageComStart; i < sleepStageComFinish; i++) {
                sleepStageAdj[i] = sleepStageSmooth[i]; // complex
            }
        }
        // ------REM修正
        int preSleepStage = 0;  //前一睡眠分期
        bool remStagePermission = false; //REM期允许出现的标志
        std::vector<int> remStageCondition = {3}; //REM期允许出现的条件
        for (int i = 0; i < sleepStageAdj.size(); i++) {
            if (std::find(remStageCondition.begin(), remStageCondition.end(), sleepStageAdj[i]) != remStageCondition.end()) {
                remStagePermission = true; //满足条件，则允许出现REM期
            }
            if (remStagePermission) {
                if (sleepStageAdj[i] == 0) {
                    remStagePermission = false; //后出现清醒期，则不可出现REM期，需再次满足条件后才允许出现
                    remStageCondition = {1, 2, 3}; //更新条件
                }
            } else {
                if (sleepStageAdj[i] == 4) {
                    sleepStageAdj[i] = preSleepStage;
                }
            }
            preSleepStage = sleepStageAdj[i];
        }
        auto wearRec = temp.wearRec;
        int awakeLen = std::count(sleepStageAdj.begin(), sleepStageAdj.end(), 0);
        awakeLen += std::count(sleepStageAdj.begin(), sleepStageAdj.end(), 1);
        int remLen = std::count(sleepStageAdj.begin(), sleepStageAdj.end(), 4);
        int deepLen = std::count(sleepStageAdj.begin(), sleepStageAdj.end(), 3);
        int lightLen = sleepStageAdj.size() - awakeLen - remLen - deepLen;
        if (std::accumulate(wearRec.begin(), wearRec.end(), 0) <= 0) {
            awakeLen = 0;
            remLen = 0;
            lightLen = 0;
            deepLen = 0;
        }
        int sleepLatency = static_cast<int>(sleepPoint * 6);
        int awakeDuration = static_cast<int>(awakeLen * 6);
        int remDuration = static_cast<int>(remLen * 6);
        int lightDuration = static_cast<int>(lightLen * 6);
        int deepDuration = static_cast<int>(deepLen * 6);

        // 佩戴检测
        for (int i = 0; i < dataLen; i++) {
            if (wearRect.at(i) == 0) {
                sleepCurveNc.at(i) = 0;
            }
        }

        //进阶分析
        // ---体动次数
        auto movementCount = mathtool::sum(temp.sleepEEGMovementRec);
        auto movementRec = temp.sleepEEGMovementRec;

        //---觉醒次数
        auto arousalCount = mathtool::sum(temp.sleepEEGArousalRec);
        auto arousalRec = temp.sleepEEGArousalRec;

        //---抗干扰能力
        double disturbTolerance = 0.;
        if (sleepPoint > 0) // 确定入睡
        {
            // 浅睡期大于0
            if (std::count(sleepStageAdj.begin(), sleepStageAdj.end(), 2) > 0) {
                auto eegSpindleRec = temp.sleepEEGSpindleRec;
                std::vector<double> eegValidSpindleRec;
                for (int i = 0; i < sleepStageAdj.size(); i++) {
                    if (sleepStageAdj[i] == 2) {
                        eegValidSpindleRec.push_back(eegSpindleRec[i]);
                    }
                }
                double eegValidSpindleAvg = 0;
                int count = 0;
                for (double i : eegValidSpindleRec) {
                    if (i > 0) {
                        eegValidSpindleAvg += i;
                        count++;
                    }
                }
                if (count > 0) {
                    eegValidSpindleAvg /= count;
                }
                disturbTolerance = std::max(0, std::min(100, static_cast<int>((eegValidSpindleAvg - 0.13) * 340)));
            }
            else
                disturbTolerance = 0.;
        }

        auto sleepCurveTmp = nc::round(sleepCurveNc, 2).toStlVector();
        res.sleepCurve.assign(sleepCurveTmp.begin(), sleepCurveTmp.end());
            //WAKE = 0  # 清醒
    //NREM1 = 1  # 非快速眼动期1（思睡期）
    //NREM2 = 2  # 非快速眼动期2（浅睡期）
    //NREM3 = 3  # 非快速眼动期3（深睡期）
    //REM = 4  # 快速眼动期
        res.sleepStage.assign(sleepStageAdj.begin(), sleepStageAdj.end());
        res.sleepPoint = int(sleepPoint);
        res.sleepLatency = sleepLatency;
        res.awakeDuration = awakeDuration;
        res.remDuration = remDuration;
        res.lightDuration = lightDuration;
        res.deepDuration = deepDuration;
        res.eegAlphaCurve.assign(temp.sleepEEGAlphaRec.begin(), temp.sleepEEGAlphaRec.end());
        res.eegBetaCurve.assign(temp.sleepEEGBetaRec.begin(), temp.sleepEEGBetaRec.end());
        res.eegThetaCurve.assign(temp.sleepEEGThetaRec.begin(), temp.sleepEEGThetaRec.end());
        res.eegDeltaCurve.assign(temp.sleepEEGDeltaRec.begin(), temp.sleepEEGDeltaRec.end());
        res.eegGammaCurve.assign(temp.sleepEEGGammaRec.begin(), temp.sleepEEGGammaRec.end());
        res.eegQualityRec.assign(temp.sleepEEGQualityRec.begin(), temp.sleepEEGQualityRec.end());
        res.eegHighBetaDBCurve.assign(temp.sleepEEGHighBetaRec.begin(), temp.sleepEEGHighBetaRec.end());
        res.eegLowBetaDBCurve.assign(temp.sleepEEGLowBetaRec.begin(), temp.sleepEEGLowBetaRec.end());
        res.movementCount = static_cast<int>(movementCount);
        res.arousalCount = static_cast<int>(movementCount);
        res.disturbTolerance = disturbTolerance;
        res.movementRec.assign(movementRec.begin(), movementRec.end());
        res.arousalRec.assign(arousalRec.begin(), arousalRec.end());
        return res;
    }

    int SleepComputing::reportLength() {
        return temp.sleepStateRec.size();
    }
}