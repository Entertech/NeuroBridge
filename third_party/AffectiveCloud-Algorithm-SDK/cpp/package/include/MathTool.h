#ifndef C9E90D92_1A1C_4EF5_AE00_EF365C5A8FCF
#define C9E90D92_1A1C_4EF5_AE00_EF365C5A8FCF
#include "TypeDefine.h"
#include <math.h>
#include "Data.h"
#include <numeric>
namespace basic
{
    namespace mathtool
    {
        /**
         * @brief 计算实时均值（利用实时的新数据更新数据缓存，并根据数据缓存计算出一个均值，可用于实时处理变化较剧烈的数据，使其更平稳）
         * 
         * @param newData 新数据（可以是一个值或者一个数组）
         * @param dataBuffer 数据缓存
         * @param bufferLim 缓存设定长度（长度越大，初始化过程越长，结果更平稳）
         * @param initialVal 初始值（缓存长度不足时返回的初始化状态值）
         * @return double 实时均值，更新后的数据缓存（达到设定长度后，更新后的缓存与输入的缓存等长）
         */
        double immediateMeanCal(vectord newData, vectord &dataBuffer, int bufferLim, double initialVal = 0);

        /**
         * @brief 硬阈值处理（可用于小波阈值去噪）
         * 
         * @param inputData 小波单层数据
         * @param thr 阈值
         */
        void hardThreshold(vectord &inputData, int thr);

        /**
         * @brief 软阈值处理（可用于小波阈值去噪）
         * 
         * @param inputData 小波单层数据
         * @param thr 阈值
         * @param order 阶数
         */
        void softThreshold(vectord &inputData, int thr = NAN, int order = 3);

        /**
         * @brief 滑动平均
         * 
         * @param newData 新数据
         * @param preSmoothValue 上一个滑动平均值
         * @param beta 滑动平均参数
         * @return double 
         */
        double smoothAvg(double newData, double preSmoothValue, double beta = 0.7);

        /**
         * @brief 脑电能量滑动平均
         * 
         * @param newPower 新的脑电能量
         * @param preSmoothPower 上一滑动平均脑电能量
         * @param beta 滑动平均参数
         * @return dsp::EEGPower 滑动平均后的脑电能量
         */
        dsp::EEGPower eegPowerSmoothAvg(dsp::EEGPower newPower, dsp::EEGPower preSmoothPower, double beta = 0.7);


        /**
         * @brief 脑电能量调整：耳后脑电->前额脑电
         * 
         * @param eegPower 脑电能量
         * @return dsp::EEGPower 调整后的脑电能量
         */
        dsp::EEGPower eegPowerAdjust(dsp::EEGPower eegPower);

        /**
         * @brief 脑电能量均值计算
         * 
         * @param powerList 脑电能量列表
         * @return dsp::EEGPower 脑电能量均值
         */
        dsp::EEGPower eegMeanPowerCal(const std::vector<dsp::EEGPower> &powerList);

        /**
         * @brief 归一化
         * 
         * @param val 输入值
         * @param maxVal 最大值
         * @param minVal 最小值
         * @param normMode 归一化模式（com: [0, 1]; sym：[-1, 1]）
         * @return vectord 归一化值
         */
        vectord valueNormalize(const vectord &val, double maxVal, double minVal, const std::string &normMode = "com");

        /**
         * @brief 特征归一化
         * 
         * @param data 特征数据（二维数组，每一列是一种特征）
         * @param normParams 归一化参数（最大值，最小值）
         * @param normMode 归一化模式（com: [0, 1]; sym：[-1, 1]）
         * @return nc::NdArray<double> 归一化后的特征数据，计算得到的归一化参数
         */
        nc::NdArray<double> featureNorm(const nc::NdArray<double> &data, nc::NdArray<double> &normParams, const std::string &normMode = "com");
        
        /**
         * @brief 平滑曲线计算
         * 
         * @param rec  数据记录
         * @param halfSmoothLen 平滑窗半长度
         * @return vectord 平滑曲线
         */
        vectord smoothCurveCal(vectord &rec, int halfSmoothLen = 9);

    } // namespace mathtool
    
} // namespace basic


#endif /* C9E90D92_1A1C_4EF5_AE00_EF365C5A8FCF */
