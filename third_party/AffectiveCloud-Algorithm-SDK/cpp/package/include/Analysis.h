#ifndef ANALYSIS_HEADER_FILE_GUARD 
#define ANALYSIS_HEADER_FILE_GUARD
#include <cstdint>
#include "TypeDefine.h"
namespace basic
{
    namespace dsp
    {
        struct SingleEEGData
        {
            vectori eeg;
        };

        struct DoubleEEGData
        {
            vectori left;
            vectori right;
        };

        struct HRData
        {
            vectori hr;
        };

        struct PEPRData
        {
            vectori pe;
            vectori pr;
        };

        /**
         *  单通道脑电数据解析
         * @param rawData 原始数据
         * @param pckNum 包数量
         * @param pckByte 包字节数
         * @param headByte 包头字节数
         * @param dataByte 单个数据的字节数
         * @return
         */
        SingleEEGData singleEegDa(const std::vector<uint8_t>& rawData, int pckNum, int pckByte = 17, int headByte = 2, int dataByte = 3);
        /**
         * @brief 双通道脑电数据解析
         *
         * @param rawData 原始数据
         * @param pckNum 包数量
         * @param pckByte 包字节数
         * @param headByte 包头字节数
         * @param dataByte 单个数据的字节数
         * @return DoubleEEGData 双通道十进制脑电数据
         */
        DoubleEEGData doubleEEGDa(std::vector<uint8_t> rawData, int pckNum, int pckByte = 20, int headByte = 2, int dataByte = 3);

        /**
         * @brief
         *
         * @param rawData 原始数据
         * @param pckNum 包数量
         * @param pckByte 包字节数
         * @param headByte 包头字节数
         * @param dataByte 单个数据的字节数
         * @return HRData 十进制心率数据
         */
        HRData hrDa(std::vector<uint8_t> rawData, int pckNum, int pckByte = 1, int headByte = 0, int dataByte = 1);

        /// 单通道压电压阻数据解析
        /// \param rawData 原始数据
        /// \param pckNum 包数量
        /// \param pckByte 包字节数
        /// \param headByte 包头字节数
        /// \param dataByte 单个数据的字节数
        /// \return PEPRData 单通道压电压阻数据
        PEPRData singlePeprDa(std::vector<uint8_t> rawData, int pckNum, int pckByte=15, int headByte=2, int dataByte=2);
    } // namespace dsp

} // namespace basic
#endif